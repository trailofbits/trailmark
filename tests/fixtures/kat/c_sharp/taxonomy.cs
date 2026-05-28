// Comprehensive C# feature taxonomy fixture.

using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace Taxonomy
{
    public interface IGreeter
    {
        string Greet(string name);
    }

    public enum Status
    {
        Pending,
        Done
    }

    public static class Math
    {
        public const int Constant = 42;

        public static int Add(int a, int b)
        {
            return a + b;
        }

        public static int Branchy(int value, string mode)
        {
            int total = 0;
            if (value > 0)
            {
                total += value;
            }
            else if (value < 0)
            {
                total -= value;
            }
            for (int i = 0; i < value; i++)
            {
                if (i % 2 == 0)
                {
                    total += i;
                }
            }
            while (total > 100)
            {
                total /= 2;
            }
            try
            {
                return int.Parse(mode) + total;
            }
            catch (FormatException)
            {
                throw new InvalidOperationException("bad mode");
            }
        }

        public static async Task<string> FetchAsync(string url)
        {
            await Task.Yield();
            return url;
        }
    }

    public class Animal
    {
        public string Name { get; set; }
        public string Species { get; set; } = "unknown";

        public Animal(string name)
        {
            Name = name;
        }

        public virtual string Describe()
        {
            return $"{Name} the {Species}";
        }
    }

    public class Dog : Animal, IGreeter
    {
        public string Breed { get; set; }

        public Dog(string name, string breed = null) : base(name)
        {
            Species = "dog";
            Breed = breed;
        }

        public string Bark(bool loud = false)
        {
            if (loud)
            {
                throw new InvalidOperationException("too loud");
            }
            return $"{Name}: woof";
        }

        public string Greet(string name)
        {
            return $"{Name} greets {name}";
        }
    }
}
